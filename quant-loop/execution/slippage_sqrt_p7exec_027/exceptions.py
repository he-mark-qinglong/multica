"""Custom exceptions for slippage_sqrt_p7exec_027.

Hierarchy::

    SlippageSqrtError
        InvalidRequestError       (also a ValueError)
        SlippageSqrtJournalWriteError
        SlippageSqrtJournalReplayRequired
        SlippageSqrtHalt

Anything caught by user code as a generic ``ValueError`` will still
see ``InvalidRequestError`` because it inherits from both
``SlippageSqrtError`` and ``ValueError``.
"""

from __future__ import annotations


class SlippageSqrtError(Exception):
    """Base class for all errors raised by this component."""


class InvalidRequestError(SlippageSqrtError, ValueError):
    """Raised when a ``SlippageSqrtRequest`` is structurally invalid.

    Inherits from :class:`ValueError` so generic value-validation
    callers also see it. The runner treats these as caller bugs;
    they are NOT caught and converted to estimates.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        # ``ValueError`` historically prints as ``ValueError: message``
        # rather than the subclass name. We keep the message self-
        # descriptive so the substring is enough to debug from logs.


class SlippageSqrtJournalWriteError(SlippageSqrtError):
    """Raised when the WAL append fails (disk full, fd closed, ...).

    The runner halts; the caller decides whether to retry. The
    calculator never silently swallows journal failures — losing the
    ability to persist would silently drop fills in the order
    journal, which the parent issue forbids.
    """


class SlippageSqrtJournalReplayRequired(SlippageSqrtError):
    """Raised at construction when the journal exists but no
    checkpoint is present (or the checkpoint is older than the
    journal). Recovery: run ``rebuild_checkpoint.py`` to materialise
    a fresh checkpoint from the journal.
    """


class SlippageSqrtHalt(SlippageSqrtError):
    """Raised when the journal contains a corrupted row, or a
    checkpoint cannot be parsed. The calculator does NOT silently
    skip; it halts so the operator can investigate.
    """