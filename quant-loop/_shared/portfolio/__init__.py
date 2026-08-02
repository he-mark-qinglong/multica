"""Portfolio management layer: multi-strategy account views, exposure
limits, performance/drawdown attribution, benchmark comparison, state
snapshots, and HTML reporting.

Opt-in library, same convention as ``_shared/sizing`` and
``_shared/attribution``: no auto-wiring into strategies or the paper
runner. Pure functions + frozen dataclasses; I/O confined to
``snapshot.py`` (parquet) and ``reporting.py`` (string generation).
"""
