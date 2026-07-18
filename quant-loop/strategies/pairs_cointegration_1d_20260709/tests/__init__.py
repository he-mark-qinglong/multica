"""Make `tests/` a real Python package so relative imports work.

Without this, `from ._synthetic import ...` raises
`ImportError: attempted relative import with no known parent package`
during pytest collection (test files load as top-level modules, not
as `tests.*`). This empty marker is the standard fix.
"""
