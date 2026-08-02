"""Package marker so tests/ can use relative imports under a unique package.

tests/ contains ``from ._synthetic import ...`` style imports; with the
repo-wide ``--import-mode=importlib`` pytest setting the test modules need a
fully-qualified package path (``pairs_cointegration_1d_20260709.tests.*``)
to keep those relative imports unambiguous across strategy directories.
"""
