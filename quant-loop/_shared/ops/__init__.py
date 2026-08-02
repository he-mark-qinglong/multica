"""Live-operations infrastructure for quant-loop.

Modules:
- structured_log   (H6)  — JSON-lines structured logger
- metrics_export   (H7)  — Prometheus text exposition exporter
- alerting         (H5)  — structured alerts with pluggable sinks
- alert_channels   (H-ch) — Telegram + Email sink implementations
- heartbeat        (H14/H15) — heartbeat writer + timeout watcher
- supervisor       (H16/H17/H4) — crash-restart supervisor, version rollback, drain
- drift_monitor    (H19) — live-vs-backtest drift monitoring
- risk_dashboard   (D19) — real-time risk monitoring HTML dashboard
- dashboard_auth   (H-auth) — token-gated HTTP layer for the dashboard
- secrets          (H13) — API key management with redaction
- deploy           (H11) — systemd / launchd unit generator
- isolation        (H-iso) — resource isolation helpers
- config_hot       (H-cfg) — hot-reload configuration
- remote_control   (H-rc) — remote control HTTP endpoint
- audit_trail      (H-audit) — audit trail logging
- pnl_attribution  (H-pnl) — PnL attribution analysis
- multi_runner     (H-mr) — multi-strategy runner
"""
