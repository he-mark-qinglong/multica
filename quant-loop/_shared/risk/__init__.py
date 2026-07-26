"""Risk-management primitives for quant-loop strategies.

Pure functions only — no I/O, no DB, no global state. Each module in this
package is a seatbelt between sizing (e.g. ``_shared/sizing/vol_target.py``)
and order routing.
"""
