"""L2 order-book replay infrastructure (B4).

Sub-modules:

* :mod:`_shared.l2.book` — immutable order-book state and snapshot+diff
  reconstruction engine.
* :mod:`_shared.l2.replay` — diff-driven replay engine that fills limit
  orders against real depth (level-walking partial fills + queue model).
* :mod:`_shared.l2.bookdepth` — loader for Binance public-data
  ``bookDepth`` parquet snapshots (data.binance.vision).
"""
