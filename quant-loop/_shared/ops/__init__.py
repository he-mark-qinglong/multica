"""Live-operations infrastructure for quant-loop.

Modules:
- structured_log  (H6)  — JSON-lines structured logger
- metrics_export  (H7)  — Prometheus text exposition exporter
- alerting        (H5)  — structured alerts with pluggable sinks
- heartbeat       (H14/H15) — heartbeat writer + timeout watcher
- supervisor      (H16/H17/H4) — crash-restart supervisor, version rollback, drain
- drift_monitor   (H19) — live-vs-backtest drift monitoring
"""
